"""DEREK'S NON-FUNDED RESEARCH OBSERVATIONS (migration 170).

── THE DEADLOCK THIS BREAKS ────────────────────────────────────────────────

Derek's internal entry model (`bettor_funded_model.KEY_ENTRY_PAYOUT`, features
`acquisition_price` and `payout_is_complement`, target
CONTRACT_PAYOUT_EVENT_OCCURRED) was trained only on `derek_entry_decisions`,
and Derek decides only ENTRY_DECISION valuations -- which exist only when the
venue read established book currency. In production no read does (P5), every
valuation is CALIBRATION_ONLY, and so no training record could ever accrue.
Yet learning how a price relates to the settlement does not need proof that
the price was executable. This module records what that learning needs,
without claiming anything about execution.

── WHAT IS RECORDED, ONCE PER VALUATION, FROZEN AT ITS DECISION ────────────

For every valuation of the entry experiment -- CALIBRATION_ONLY and
ENTRY_DECISION alike -- one row in `derek_research_observations`, built only
from that valuation's decision-time fields:

  * the fixture, event key, market slug, intent and payout side;
  * the price the model's feature uses, WITH ITS BASIS AND COHORT
      CALIBRATION_ONLY -> DISPLAYED_BOOK_CURRENCY_UNESTABLISHED,
                          cohort DISPLAYED_PRICE_AGE_UNKNOWN
                          (the displayed price in the record's calibration
                          evidence, usable_for_orders false)
      ENTRY_DECISION   -> EXECUTABLE_BOOK_CURRENCY_ESTABLISHED,
                          cohort EXECUTABLE_PRICE_CURRENT;
  * the price's TIMING: our LOCAL RECEIPT instant (our clock), the venue's
    SOURCE TIMESTAMP only when the venue supplied one (NULL otherwise, with
    the reason -- never defaulted, never inferred from an age), and the
    TIMING UNCERTAINTY with its basis;
  * the price's SOURCE IDENTITY (venue, endpoint, market slug, intent, side);
  * the CONTEMPORANEOUS Pinnacle reading the valuation was made with
    (probability, observed_at, received_at, overround, method, version);
  * the feature vector and its sha;
  * the approved model's prediction when a model was approved at the
    decision instant, otherwise why not.

── WHAT IT CANNOT DO ───────────────────────────────────────────────────────

It cannot authorize, size or send an order. The table has no quantity, size,
limit, verdict, plan, admission, fill, depth or edge column;
`price_usable_for_orders` is CHECKed false and `execution_quality` CHECKed
'UNKNOWN' on every row. Nothing here imports an execution module, and no
execution module names the table (census in
tests/test_derek_research_observations_break_the_deadlock.py). The model
registry reads it to fit, evaluate and re-verify a model's training records;
the only thing that leaves this module is a probability.

── INDEPENDENCE ────────────────────────────────────────────────────────────

Collection depends on nothing but the valuation rows: not on model approval
(an absent or unverifiable model only leaves `model_p` NULL with its reason),
not on account authorization, not on trade admission, not on the submission
switches. It runs every scheduled cycle from `derek.after_cycle`, bounded
(MAX_PER_CYCLE) and idempotent per valuation.
"""

from __future__ import annotations

import json
import time
from typing import Any

OBSERVER_VERSION = "DEREK_RESEARCH_OBSERVER_V1"

#: Rows older than this are not picked up as "this cycle's" (the same window
#: Derek's own decisions use), so a restarted process observes the recent
#: record rather than replaying history. `observe` takes any window.
LOOKBACK_S = 2 * 900.0
MAX_PER_CYCLE = 500

#: THE ONLY OUTCOME BASES THAT ARE LABELS: a settled 0/1 read from the venue.
#: A confirmed void carries its own basis and outcome_known FALSE; an outcome
#: written without a basis is not verified. Neither is a label.
LABEL_BASES = ("VENUE_SETTLEMENT_PRICE", "VENUE_REPORTED_OUTCOME")

#: WHERE THE VENUE PRICE CAME FROM. Both purposes price off the same read:
#: `ext_pinnacle_loop.venue_quote` -> `_read_book_blocking` ->
#: `pmus.book_read(slug)`, the REST book (`marketData`). A subscription, when
#: one exists, only establishes the book's currency; it is not the price.
VENUE_BOOK_ENDPOINT = ("PMUS REST book: pmus.book_read(<market slug>)"
                       ".marketData, read by ext_pinnacle_loop.venue_quote")

#: ── TIMING UNCERTAINTY ──────────────────────────────────────────────────
TIMING_AGE_UNKNOWN = "AGE_BOUND_UNKNOWN"
TIMING_BOUNDED = "AGE_BOUNDED_BY_ESTABLISHED_BOOK_CURRENCY"
TIMING_NOT_RECORDED = "AGE_BOUND_NOT_RECORDED_ON_THE_VALUATION"
TIMINGS = (TIMING_AGE_UNKNOWN, TIMING_BOUNDED, TIMING_NOT_RECORDED)

#: ── WHY A VALUATION WAS NOT OBSERVED (counted, never silent) ────────────
S_UNKNOWN_PURPOSE = "VALUATION_PURPOSE_NOT_RECOGNISED"
S_NO_PRICE = "NO_PRICE_ON_THE_VALUATION"
S_PRICE_OUT_OF_RANGE = "PRICE_NOT_STRICTLY_BETWEEN_0_AND_1"
S_NO_PINNACLE = "NO_PINNACLE_PROBABILITY_ON_THE_VALUATION"
S_NO_FIXTURE = "NO_FIXTURE_IDENTITY_ON_THE_VALUATION"
#: A BACKFILL is retrospective evidence only where the stored row holds the
#: original timestamps; otherwise it is excluded, and counted.
S_BACKFILL_NO_PRICE_RECEIPT = \
    "BACKFILL_EXCLUDED_STORED_ROW_HOLDS_NO_PRICE_RECEIPT_TIME"
S_BACKFILL_NO_PINNACLE_STAMP = \
    "BACKFILL_EXCLUDED_STORED_ROW_HOLDS_NO_PINNACLE_OBSERVED_AT"
S_BACKFILL_NO_DECISION_TIME = \
    "BACKFILL_EXCLUDED_STORED_ROW_HOLDS_NO_DECISION_TIME"

#: ── HOW AN OBSERVATION WAS COLLECTED (CHECKed by migration 170) ──────────
MODE_LIVE = "LIVE_CYCLE"
MODE_BACKFILL = "BACKFILL_FROM_STORED_VALUATION"
#: THE ONE-TIME BACKFILL: older valuations of either purpose, walked
#: BACKWARDS by id from the live window's lower edge, at most this many per
#: cycle, until none remain. Its cursor lives in ingestion_state.
BACKFILL_PER_CYCLE = 2000
BACKFILL_STATE_KEY = "derek_research_backfill"

A_NO_APPROVED_MODEL = "NO_APPROVED_MODEL_AT_THE_DECISION_INSTANT"
A_BACKFILL = ("BACKFILLED_FROM_THE_STORED_VALUATION: no prediction was frozen "
              "at the decision instant and none is inferred afterwards")
A_APPROVED_AFTER = "MODEL_APPROVED_AFTER_THE_DECISION_INSTANT"
A_REGISTRY_UNREADABLE = "MODEL_REGISTRY_UNREADABLE"
A_CANNOT_SCORE = "APPROVED_MODEL_COULD_NOT_SCORE_THE_VECTOR"


def _FM():
    from .. import bettor_funded_model as FM
    return FM


def _j(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _epoch(v) -> float | None:
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    return _f(v)


def cohort_of(purpose) -> str | None:
    FM = _FM()
    return {"CALIBRATION_ONLY": FM.COHORT_DISPLAYED,
            "ENTRY_DECISION": FM.COHORT_EXECUTABLE}.get(str(purpose or ""))


def basis_of(purpose) -> str | None:
    FM = _FM()
    return {"CALIBRATION_ONLY": FM.PRICE_BASIS_DISPLAYED,
            "ENTRY_DECISION": FM.PRICE_BASIS_EXECUTABLE}.get(str(purpose or ""))


# ═════════════════════════════════════════════════════════════════════════
# 1 · ONE OBSERVATION, FROM ONE VALUATION ROW (pure)
# ═════════════════════════════════════════════════════════════════════════

def _price_and_timing(row: dict) -> dict:
    """The price the feature uses and everything known about when it was
    seen, from the valuation's own record. Nothing is inferred: a clock the
    record does not carry is NULL with the reason."""
    purpose = row.get("record_purpose")
    out: dict[str, Any] = {"price": None, "price_source": None,
                           "received_at": None, "source_ts": None,
                           "source_ts_basis": None, "timing": None,
                           "timing_basis": None, "identity": {}}
    slug = row.get("us_market_slug")
    ident = {"venue": row.get("venue"), "endpoint": VENUE_BOOK_ENDPOINT,
             "market_slug": slug, "intent": row.get("buy_intent"),
             "ladder_side": row.get("ladder_side"),
             "payout_event": row.get("payout_event"),
             "payout_is_complement": bool(row.get("payout_is_complement"))}
    if purpose == "CALIBRATION_ONLY":
        ev = _j(row.get("calibration_only_evidence")) or {}
        cmp_ = dict(ev.get("compared_at_the_displayed_price") or {})
        shown = dict(ev.get("displayed_quote") or {})
        if cmp_.get("price") is not None:
            out["price"] = _f(cmp_.get("price"))
            out["price_source"] = ("calibration_only_evidence."
                                   "compared_at_the_displayed_price.price")
        elif shown.get("acquisition_price") is not None:
            out["price"] = _f(shown.get("acquisition_price"))
            out["price_source"] = ("calibration_only_evidence."
                                   "displayed_quote.acquisition_price")
        out["received_at"] = _f(shown.get("read_at"))
        vts = _f(shown.get("venue_ts"))
        out["source_ts"] = vts
        out["source_ts_basis"] = (
            "VENUE_TRANSACT_TIME (marketData.transactTime; what the venue "
            "means by it is unresolved, so it is provenance, not an age)"
            if vts is not None else
            ("NOT_SUPPLIED_BY_THE_VENUE: %s" % shown.get("venue_clock_basis")
             if shown.get("venue_clock_basis") else
             "NOT_CARRIED_ON_THE_VALUATION_RECORD (written before the "
             "displayed quote carried the venue clock)"))
        cur = dict(ev.get("book_currency") or {})
        out["timing"] = TIMING_AGE_UNKNOWN
        out["timing_basis"] = (
            "book currency %s (mechanism %s): no mechanism with a published "
            "contract bounds how old the displayed book was. Our receipt "
            "instant bounds only our own delay, and the venue stamp's meaning "
            "is unresolved" % (cur.get("verdict") or shown.get(
                "book_currency_verdict"), cur.get("mechanism")
                or shown.get("book_currency_mechanism")))
        ident.update(market_slug=shown.get("slug") or slug,
                     intent=shown.get("intent") or row.get("buy_intent"),
                     side_consumed=shown.get("side_consumed"),
                     pays_on=shown.get("pays_on"))
    elif purpose == "ENTRY_DECISION":
        out["price"] = _f(row.get("executable_price"))
        out["price_source"] = "valuation.executable_price"
        risk = _j(row.get("risk_verdict")) or {}
        fr = dict(risk.get("freshness_evidence") or {})
        clock = dict(fr.get("venue_clock") or {})
        out["received_at"] = _f(clock.get("our_response_received_at"))
        vts = _f(clock.get("parsed_epoch_s"))
        out["source_ts"] = vts
        out["source_ts_basis"] = (
            "VENUE_TRANSACT_TIME (marketData.transactTime; provenance)"
            if vts is not None else
            ("NOT_SUPPLIED_BY_THE_VENUE: %s" % clock.get("basis")
             if clock.get("basis") else
             "NOT_CARRIED_ON_THE_VALUATION_RECORD"))
        age, lim = _f(fr.get("venue_age_s")), _f(fr.get("venue_limit_s"))
        if age is not None and lim is not None:
            out["timing"] = TIMING_BOUNDED
            out["timing_basis"] = (
                "book state age %.3f s <= %.3f s, established by %s at the "
                "decision" % (age, lim, fr.get("venue_age_basis")))
        else:
            out["timing"] = TIMING_NOT_RECORDED
            out["timing_basis"] = (
                "an ENTRY_DECISION row exists only when currency was "
                "established, but this record carries no book-state age")
        ident["side_consumed"] = row.get("ladder_side")
    out["identity"] = ident
    return out


def _frozen_model(approved: dict | None, feats: dict, *,
                  decided_at: float | None) -> dict:
    """The approved model's prediction on this vector, if one was approved
    at the decision instant. Pure given the registry read."""
    FM = _FM()
    ap = dict(approved or {})
    if not ap.get("ok"):
        unreadable = ("error" in ap
                      or ap.get("refusal") == FM.R_SCHEMA_UNAVAILABLE)
        return {"model_p": None, "model_id": None, "model_version": None,
                "absent": "%s (%s)" % (
                    A_REGISTRY_UNREADABLE if unreadable
                    else A_NO_APPROVED_MODEL,
                    ap.get("error") or ap.get("refusal"))}
    m = dict(ap.get("model") or {})
    appr = _epoch(m.get("approved_at"))
    if decided_at is not None and appr is not None and appr > decided_at:
        return {"model_p": None, "model_id": None, "model_version": None,
                "absent": "%s (%s approved at %s)" % (
                    A_APPROVED_AFTER, m.get("model_id"), appr)}
    try:
        p = float(FM.load(m["params"]).predict(feats))
    except Exception as exc:                                   # noqa: BLE001
        return {"model_p": None, "model_id": None, "model_version": None,
                "absent": "%s (%s)" % (A_CANNOT_SCORE, type(exc).__name__)}
    return {"model_p": min(1.0, max(0.0, p)), "model_id": m.get("model_id"),
            "model_version": m.get("model_version"), "absent": None}


def observation_from_row(row: dict, *, approved: dict | None = None,
                         mode: str = MODE_LIVE
                         ) -> tuple[dict | None, str | None]:
    """(observation, None) or (None, the named reason it is not one).

    Every value comes from the valuation row as stored -- the same for a
    live observation and a backfilled one. A BACKFILL freezes no model
    prediction: none was made at the decision, and one made now would be
    an inference about the past."""
    from . import derek_policy as DP
    FM = _FM()
    r = dict(row or {})
    purpose = r.get("record_purpose")
    cohort, basis = cohort_of(purpose), basis_of(purpose)
    if cohort is None:
        return None, S_UNKNOWN_PURPOSE
    fixture = DP.fixture_of(condition_id=r.get("condition_id"),
                            event_key=r.get("event_key"),
                            us_market_slug=r.get("us_market_slug"))
    if not fixture:
        return None, S_NO_FIXTURE
    pt = _price_and_timing(r)
    price = pt["price"]
    if price is None:
        return None, S_NO_PRICE
    if not (0.0 < price < 1.0):
        return None, S_PRICE_OUT_OF_RANGE
    pin = _f(r.get("probability"))
    if pin is None:
        return None, S_NO_PINNACLE
    decided = _epoch(r.get("decided_at"))
    if mode != MODE_LIVE:
        # RETROSPECTIVE ONLY WHERE THE ORIGINAL TIMESTAMPS WERE STORED.
        if decided is None:
            return None, S_BACKFILL_NO_DECISION_TIME
        if pt["received_at"] is None:
            return None, S_BACKFILL_NO_PRICE_RECEIPT
        if _epoch(r.get("observed_at")) is None:
            return None, S_BACKFILL_NO_PINNACLE_STAMP
    feats = {"acquisition_price": round(float(price), 9),
             "payout_is_complement": (1.0 if r.get("payout_is_complement")
                                      else 0.0)}
    mdl = (_frozen_model(approved, feats, decided_at=decided)
           if mode == MODE_LIVE else
           {"model_p": None, "model_id": None, "model_version": None,
            "absent": A_BACKFILL})
    vid = int(r["id"])
    return {
        "observation_id": "derek-research:val:%d" % vid,
        "valuation_id": vid,
        "experiment_id": r.get("experiment_id"),
        "record_purpose": purpose, "cohort": cohort,
        "fixture": fixture, "event_key": r.get("event_key"),
        "condition_id": r.get("condition_id"),
        "us_market_slug": r.get("us_market_slug"),
        "buy_intent": r.get("buy_intent"),
        "payout_event": r.get("payout_event"),
        "payout_is_complement": bool(r.get("payout_is_complement")),
        "decided_at": decided,
        "price": float(price), "price_basis": basis,
        "price_source": pt["price_source"],
        "price_received_at": pt["received_at"],
        "price_source_ts": pt["source_ts"],
        "price_source_ts_basis": pt["source_ts_basis"],
        "price_timing_uncertainty": pt["timing"],
        "price_timing_basis": pt["timing_basis"],
        "price_source_identity": pt["identity"],
        "pinnacle_p": pin,
        "pinnacle_observed_at": _epoch(r.get("observed_at")),
        "pinnacle_received_at": _epoch(r.get("received_at")),
        "pinnacle_overround": _f(r.get("overround")),
        "devig_method": r.get("devig_method"),
        "pinnacle_source_version": r.get("version"),
        "features": feats, "feature_sha": FM.feature_sha(feats),
        "model_id": mdl["model_id"], "model_version": mdl["model_version"],
        "model_p": mdl["model_p"], "model_absent_reason": mdl["absent"],
        "collection_mode": mode,
        "evidence_class": (FM.EVIDENCE_CLASS_LIVE if mode == MODE_LIVE
                           else FM.EVIDENCE_CLASS_STORED),
    }, None


# ═════════════════════════════════════════════════════════════════════════
# 2 · RECORDING (the scheduled path calls `observe_cycle`)
# ═════════════════════════════════════════════════════════════════════════

_SELECT = """
    SELECT v.id, v.experiment_id, v.record_purpose, v.venue,
           extract(epoch FROM v.decided_at) AS decided_at,
           v.event_key, v.condition_id, v.us_market_slug, v.buy_intent,
           v.ladder_side, v.payout_event, v.payout_is_complement,
           v.probability, v.overround, v.devig_method, v.version,
           extract(epoch FROM v.observed_at) AS observed_at,
           extract(epoch FROM v.received_at) AS received_at,
           v.executable_price, v.risk_verdict, v.calibration_only_evidence
      FROM external_valuations v
     WHERE v.experiment_id = $1
       AND v.record_purpose IN ('CALIBRATION_ONLY', 'ENTRY_DECISION')
       AND NOT EXISTS (SELECT 1 FROM derek_research_observations o
                        WHERE o.valuation_id = v.id)
"""

#: THIS CYCLE'S WINDOW, oldest first.
CANDIDATES_SQL = _SELECT + """
       AND v.decided_at > to_timestamp($2)
       AND v.decided_at <= to_timestamp($3)
     ORDER BY v.id
     LIMIT $4
"""

#: THE BACKFILL: older than the live window, BACKWARDS by id from the cursor
#: (NULL: from the newest), so the most recent history arrives first.
BACKFILL_SQL = _SELECT + """
       AND v.decided_at <= to_timestamp($2)
       AND ($3::bigint IS NULL OR v.id < $3::bigint)
     ORDER BY v.id DESC
     LIMIT $4
"""

INSERT_SQL = """
    INSERT INTO derek_research_observations
        (observation_id, valuation_id, experiment_id, record_purpose, cohort,
         fixture, event_key, condition_id, us_market_slug, buy_intent,
         payout_event, payout_is_complement, decided_at,
         price, price_basis, price_source, price_received_at,
         price_source_ts, price_source_ts_basis, price_timing_uncertainty,
         price_timing_basis, price_source_identity,
         pinnacle_p, pinnacle_observed_at, pinnacle_received_at,
         pinnacle_overround, devig_method, pinnacle_source_version,
         features, feature_sha, model_id, model_version, model_p,
         model_absent_reason, observer_version, collection_mode,
         evidence_class)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,to_timestamp($13),
            $14,$15,$16,
            CASE WHEN $17::float8 IS NULL THEN NULL ELSE to_timestamp($17) END,
            CASE WHEN $18::float8 IS NULL THEN NULL ELSE to_timestamp($18) END,
            $19,$20,$21,$22::jsonb,$23,
            CASE WHEN $24::float8 IS NULL THEN NULL ELSE to_timestamp($24) END,
            CASE WHEN $25::float8 IS NULL THEN NULL ELSE to_timestamp($25) END,
            $26,$27,$28,$29::jsonb,$30,$31,$32,$33,$34,$35,$36,$37)
    ON CONFLICT DO NOTHING
    RETURNING observation_id
"""


def _insert_args(o: dict) -> tuple:
    return (o["observation_id"], o["valuation_id"], o["experiment_id"],
            o["record_purpose"], o["cohort"], o["fixture"], o["event_key"],
            o["condition_id"], o["us_market_slug"], o["buy_intent"],
            o["payout_event"], o["payout_is_complement"],
            float(o["decided_at"] if o["decided_at"] is not None
                  else time.time()),
            o["price"], o["price_basis"], o["price_source"],
            o["price_received_at"], o["price_source_ts"],
            o["price_source_ts_basis"], o["price_timing_uncertainty"],
            o["price_timing_basis"],
            json.dumps(o["price_source_identity"], default=str),
            o["pinnacle_p"], o["pinnacle_observed_at"],
            o["pinnacle_received_at"], o["pinnacle_overround"],
            o["devig_method"], o["pinnacle_source_version"],
            json.dumps(o["features"]), o["feature_sha"], o["model_id"],
            o["model_version"], o["model_p"], o["model_absent_reason"],
            OBSERVER_VERSION, o.get("collection_mode") or MODE_LIVE,
            o.get("evidence_class") or _FM().EVIDENCE_CLASS_LIVE)


async def _regclass(conn, name) -> bool:
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                        name))
    except Exception:                                          # noqa: BLE001
        return False


async def observe(conn, *, since: float, until: float,
                  limit: int = MAX_PER_CYCLE) -> dict:
    """Record one research observation per entry-experiment valuation decided
    in (since, until] that has none yet. Bounded by `limit`, idempotent per
    valuation, never raises. Reads no account, no switch, no admission."""
    from .. import bettor_external_shadow as ext
    out: dict[str, Any] = {"observer_version": OBSERVER_VERSION,
                           "window": [float(since), float(until)],
                           "limit": int(limit), "candidates": 0,
                           "recorded": 0, "already_recorded": 0,
                           "not_observed": {}, "by_cohort": {},
                           "model_frozen": 0, "errors": {}}
    if not await _regclass(conn, "derek_research_observations"):
        return dict(out, ok=False, refusal="RESEARCH_TABLE_ABSENT",
                    why="migration 170 is not applied here")
    try:
        rows = [dict(r) for r in await conn.fetch(
            CANDIDATES_SQL, ext.EXPERIMENT_ID, float(since), float(until),
            int(limit))]
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, ok=False, refusal="CANDIDATES_READ_FAILED",
                    error=type(exc).__name__)
    out["candidates"] = len(rows)
    out["bound_reached"] = len(rows) >= int(limit)
    approved = None
    if rows:
        # THE REGISTRY'S OWN VERIFIED READ. Its absence or failure only
        # leaves the frozen prediction empty; it never stops collection.
        try:
            from . import derek_policy as DP
            approved = await DP.approved_entry_model(conn)
        except Exception as exc:                               # noqa: BLE001
            approved = {"ok": False, "error": type(exc).__name__}
    await _record_rows(conn, rows, out, approved=approved, mode=MODE_LIVE)
    out["ok"] = not out["errors"]
    return out


async def _record_rows(conn, rows, out: dict, *, approved, mode) -> None:
    for row in rows:
        try:
            obs, why = observation_from_row(row, approved=approved,
                                            mode=mode)
            if obs is None:
                out["not_observed"][why] = out["not_observed"].get(why, 0) + 1
                continue
            got = await conn.fetchval(INSERT_SQL, *_insert_args(obs))
        except Exception as exc:                               # noqa: BLE001
            k = type(exc).__name__
            out["errors"][k] = out["errors"].get(k, 0) + 1
            out.setdefault("error_sample", str(exc)[:200])
            continue
        if got is None:
            out["already_recorded"] += 1
            continue
        out["recorded"] += 1
        out["by_cohort"][obs["cohort"]] = \
            out["by_cohort"].get(obs["cohort"], 0) + 1
        if obs["model_p"] is not None:
            out["model_frozen"] += 1


async def _state(conn) -> dict | None:
    """The backfill's cursor, or None where ingestion_state is absent."""
    if not await _regclass(conn, "ingestion_state"):
        return None
    raw = await conn.fetchval("SELECT value FROM ingestion_state WHERE key=$1",
                              BACKFILL_STATE_KEY)
    return dict(_j(raw) or {})


async def _save_state(conn, state: dict) -> None:
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        BACKFILL_STATE_KEY, json.dumps(state, default=str))


async def backfill(conn, *, older_than: float, now: float,
                   limit: int = BACKFILL_PER_CYCLE) -> dict:
    """THE ONE-TIME BACKFILL, one bounded step: up to `limit` stored
    valuations decided at or before `older_than` that have no observation,
    walked BACKWARDS by id from the cursor. Each becomes a
    BACKFILL_FROM_STORED_VALUATION observation built from the stored row
    alone (recorded_at is now; the valuation id is kept). When a step finds
    nothing left the backfill is EXHAUSTED and later cycles skip it.
    Idempotent twice over: the cursor only moves down, and a valuation that
    already has an observation is never selected or inserted again. Never
    raises."""
    from .. import bettor_external_shadow as ext
    out: dict[str, Any] = {"mode": MODE_BACKFILL, "limit": int(limit),
                           "candidates": 0, "recorded": 0,
                           "already_recorded": 0, "not_observed": {},
                           "by_cohort": {}, "model_frozen": 0, "errors": {}}
    if not await _regclass(conn, "derek_research_observations"):
        return dict(out, ok=False, refusal="RESEARCH_TABLE_ABSENT")
    try:
        state = await _state(conn)
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, ok=False, refusal="BACKFILL_STATE_UNREADABLE",
                    error=type(exc).__name__)
    if state is not None and state.get("exhausted"):
        return dict(out, ok=True, exhausted=True, skipped=True,
                    exhausted_at=state.get("exhausted_at"),
                    backfilled_total=state.get("backfilled_total"))
    cursor = None if state is None else state.get("cursor_below_id")
    try:
        rows = [dict(r) for r in await conn.fetch(
            BACKFILL_SQL, ext.EXPERIMENT_ID, float(older_than),
            None if cursor is None else int(cursor), int(limit))]
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, ok=False, refusal="BACKFILL_READ_FAILED",
                    error=type(exc).__name__)
    out["candidates"] = len(rows)
    await _record_rows(conn, rows, out, approved=None, mode=MODE_BACKFILL)
    out["ok"] = not out["errors"]
    if state is not None and out["ok"]:
        # THE CURSOR MOVES ONLY PAST ROWS THAT WERE HANDLED (recorded, or
        # refused by name); a step with errors is retried whole next cycle.
        new = dict(state)
        new["backfilled_total"] = int(state.get("backfilled_total") or 0) \
            + out["recorded"]
        if rows:
            new["cursor_below_id"] = min(int(r["id"]) for r in rows)
            new["last_step_at"] = float(now)
        else:
            new.update(exhausted=True, exhausted_at=float(now))
        try:
            await _save_state(conn, new)
        except Exception as exc:                               # noqa: BLE001
            out["state_write_failed"] = type(exc).__name__
        out["cursor_below_id"] = new.get("cursor_below_id")
        out["exhausted"] = bool(new.get("exhausted"))
    return out


async def observe_cycle(conn, *, now: float, elapsed_s: float = 0.0) -> dict:
    """THE SCHEDULED STEP (`derek.after_cycle`): this cycle's window, LIVE,
    then one bounded BACKFILL step below it."""
    at = float(now)
    since = at - max(LOOKBACK_S, float(elapsed_s or 0.0) + 60.0)
    got = await observe(conn, since=since, until=at + 1.0)
    try:
        got["backfill"] = await backfill(conn, older_than=since, now=at)
    except Exception as exc:                                   # noqa: BLE001
        got["backfill"] = {"ok": False,
                           "refusal": "BACKFILL_RAISED:%s" % type(exc).__name__}
    return got


# ═════════════════════════════════════════════════════════════════════════
# 3 · THE LABELLER (called by bettor_funded_model.labelled)
# ═════════════════════════════════════════════════════════════════════════

LABEL_SQL = """
    SELECT o.observation_id, o.fixture, o.features, o.feature_sha,
           o.price, o.price_basis, o.cohort, o.pinnacle_p, o.record_purpose,
           o.evidence_class,
           extract(epoch FROM o.recorded_at) AS recorded_epoch,
           extract(epoch FROM o.decided_at) AS decided_epoch,
           v.id AS valuation_id, v.outcome, v.outcome_basis,
           extract(epoch FROM v.outcome_at) AS outcome_epoch
      FROM derek_research_observations o
      JOIN external_valuations v ON v.id = o.valuation_id
     WHERE v.experiment_id = $1
       AND v.record_purpose = o.record_purpose
       AND v.outcome_known AND v.outcome IN (0, 1)
       AND v.outcome_basis = ANY($2::text[])
       AND v.outcome_at IS NOT NULL
"""


async def labelled_observations(conn, *, after=None, through=None,
                                outcomes_through=None, decision_ids=None,
                                cohorts=None) -> dict:
    """EVERY RESEARCH OBSERVATION WHOSE CONTRACT HAS RESOLVED: the frozen
    vector, and the label from the valuation's own settlement join (1 = the
    event this contract pays on occurred). A void, an unjoined outcome, or an
    outcome without a verified venue basis is not a label. Same shape as
    `derek_policy.labelled_entries`, plus the price, its basis and its cohort
    per row. `decision_ids` are observation ids."""
    from .. import bettor_external_shadow as ext
    FM = _FM()
    out: dict[str, Any] = {"version": OBSERVER_VERSION, "rows": [],
                           "labels": []}
    keys = ("decision_ids", "groups", "fixtures", "decided_at",
            "feature_shas", "outcome_available_at", "leg_outcomes",
            "pushes", "outcome_versions", "pinnacle_p", "price_basis",
            "cohorts", "evidence_classes", "recorded_at")
    for k in keys:
        out[k] = []
    sql, args = LABEL_SQL, [ext.EXPERIMENT_ID, list(LABEL_BASES)]
    if after is not None:
        args.append(_epoch(after))
        sql += " AND extract(epoch FROM o.decided_at) > $%d" % len(args)
    if through is not None:
        args.append(_epoch(through))
        sql += " AND extract(epoch FROM o.decided_at) <= $%d" % len(args)
    if outcomes_through is not None:
        args.append(_epoch(outcomes_through))
        sql += " AND extract(epoch FROM v.outcome_at) <= $%d" % len(args)
    if decision_ids is not None:
        args.append([str(x) for x in decision_ids])
        sql += " AND o.observation_id = ANY($%d::text[])" % len(args)
    if cohorts is not None:
        args.append([str(x) for x in cohorts])
        sql += " AND o.cohort = ANY($%d::text[])" % len(args)
    sql += " ORDER BY o.decided_at, o.observation_id"
    try:
        got = await conn.fetch(sql, *args)
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, ok=False, refusal="THE_LABELS_COULD_NOT_BE_READ",
                    error="%s: %s" % (type(exc).__name__, str(exc)[:200]))
    for r in got:
        out["rows"].append(_j(r["features"]) or {})
        out["labels"].append(float(r["outcome"]))
        out["decision_ids"].append(r["observation_id"])
        out["groups"].append(r["observation_id"])
        out["fixtures"].append(r["fixture"])
        out["decided_at"].append(float(r["decided_epoch"]))
        out["feature_shas"].append(r["feature_sha"])
        out["outcome_available_at"].append(float(r["outcome_epoch"]))
        out["leg_outcomes"].append([{
            "valuation_id": int(r["valuation_id"]),
            "outcome": int(r["outcome"]),
            "outcome_basis": r["outcome_basis"],
            "read_at": round(float(r["outcome_epoch"]), 6)}])
        out["pushes"].append(False)
        out["outcome_versions"].append(None)
        out["pinnacle_p"].append(_f(r["pinnacle_p"]))
        out["price_basis"].append(r["price_basis"])
        out["cohorts"].append(r["cohort"])
        out["evidence_classes"].append(r["evidence_class"])
        out["recorded_at"].append(float(r["recorded_epoch"]))
    out["n_events"] = len({str(f) for f in out["fixtures"]})
    return dict(out, ok=True, refusal=None, n=len(out["labels"]),
                target=FM.TARGET_ENTRY_PAYOUT,
                label_basis=("the valuation's own settlement join, outcome "
                             "basis one of %s; a void, an unjoined outcome or "
                             "an unverified basis is not a label"
                             % (list(LABEL_BASES),)))


# ═════════════════════════════════════════════════════════════════════════
# 4 · MISSING-DATA COVERAGE (for the evaluation report)
# ═════════════════════════════════════════════════════════════════════════

COVERAGE_SQL = """
    SELECT v.record_purpose,
           coalesce('condition:' || v.condition_id, 'event:' || v.event_key,
                    'slug:' || v.us_market_slug) AS fixture,
           bool_or(v.probability IS NOT NULL) AS any_pinnacle,
           bool_or(CASE WHEN v.record_purpose = 'ENTRY_DECISION'
                        THEN v.executable_price IS NOT NULL
                        ELSE coalesce(v.calibration_only_evidence
                               -> 'compared_at_the_displayed_price'
                               ->> 'price',
                               v.calibration_only_evidence
                               -> 'displayed_quote'
                               ->> 'acquisition_price') IS NOT NULL
                   END) AS any_price,
           bool_or(v.outcome_known AND v.outcome IN (0, 1)
                   AND v.outcome_basis = ANY($3::text[])) AS any_label,
           bool_or(v.outcome_basis = 'CONFIRMED_VOID') AS any_void,
           bool_or(v.outcome_known AND (v.outcome_basis IS NULL
                   OR NOT v.outcome_basis = ANY($3::text[])))
               AS any_unverified_outcome,
           bool_or(o.valuation_id IS NOT NULL) AS any_observed,
           count(*) AS rows
      FROM external_valuations v
      LEFT JOIN derek_research_observations o ON o.valuation_id = v.id
     WHERE v.experiment_id = $1
       AND v.record_purpose IN ('CALIBRATION_ONLY', 'ENTRY_DECISION')
       AND v.decided_at > to_timestamp($2)
     GROUP BY 1, 2
"""


async def prospective_coverage(conn, *, after) -> dict:
    """Every entry-experiment valuation decided after `after`, by cohort and
    FIXTURE: how many fixtures lack a Pinnacle reading, a price, or a label
    (void and unverified outcomes named apart), and how many were observed.
    The universe the model's prospective cohort is drawn from, so a reader
    can see what the scored fixtures are a subset of. Never raises."""
    from .. import bettor_external_shadow as ext
    out: dict[str, Any] = {"after_epoch_s": _epoch(after), "unit":
                           "DISTINCT_FIXTURES (rows beside)", "by_cohort": {}}
    try:
        got = await conn.fetch(COVERAGE_SQL, ext.EXPERIMENT_ID,
                               _epoch(after), list(LABEL_BASES))
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, ok=False, error=type(exc).__name__)
    for r in got:
        c = cohort_of(r["record_purpose"])
        b = out["by_cohort"].setdefault(c, {
            "fixtures": 0, "rows": 0, "fixtures_lacking_pinnacle": 0,
            "fixtures_lacking_price": 0, "fixtures_lacking_label": 0,
            "of_which_void": 0, "of_which_outcome_without_verified_basis": 0,
            "fixtures_observed": 0})
        b["fixtures"] += 1
        b["rows"] += int(r["rows"])
        b["fixtures_lacking_pinnacle"] += 0 if r["any_pinnacle"] else 1
        b["fixtures_lacking_price"] += 0 if r["any_price"] else 1
        if not r["any_label"]:
            b["fixtures_lacking_label"] += 1
            b["of_which_void"] += 1 if r["any_void"] else 0
            b["of_which_outcome_without_verified_basis"] += (
                1 if r["any_unverified_outcome"] else 0)
        b["fixtures_observed"] += 1 if r["any_observed"] else 0
    return dict(out, ok=True)


# ═════════════════════════════════════════════════════════════════════════
# 5 · DEREK'S DAILY MODEL RUN: FIT AND EVALUATE, NEVER PROMOTE
# ═════════════════════════════════════════════════════════════════════════

RUN_INSUFFICIENT = "INSUFFICIENT_LABELLED_FIXTURES"
RUN_FITTED = "FITTED_AND_EVALUATED"
RUN_EVALUATED = "EVALUATED_WITHOUT_REFIT"
RUN_FIT_REFUSED = "FIT_REFUSED"
RUN_LABELS_UNREADABLE = "LABELS_UNREADABLE"
#: Scheduled candidates are named for their cohort and day, so a person can
#: see which were fitted by the schedule. The schedule NEVER promotes.
AUTO_MODEL_PREFIX = "derek-research-auto"
#: At most this many of a cohort's scheduled candidates are re-evaluated per
#: run (newest first).
EVALUATE_LATEST = 3
NEVER_PROMOTES = ("NOT_ON_A_SCHEDULE: promotion is a NAMED PERSON's act "
                  "through bettor_funded_model.promote; this run only fits, "
                  "registers CANDIDATES and evaluates them")


def _day_of(epoch: float):
    import datetime as _dt
    return _dt.datetime.fromtimestamp(float(epoch), _dt.timezone.utc).date()


def labelled_counts(lab: dict) -> dict:
    """The exact have/need, per cohort, in FIXTURES (rows beside)."""
    FM = _FM()
    per: dict = {}
    for c, fx in zip(lab.get("cohorts") or [], lab.get("fixtures") or []):
        b = per.setdefault(c, {"rows": 0, "fx": set()})
        b["rows"] += 1
        b["fx"].add(str(fx))
    need = FM.MIN_TRAIN_EVENTS
    return {"unit": "FIXTURES (distinct events), rows beside",
            "need_labelled_training_fixtures": need,
            "need_prospective_fixtures": FM.MIN_EVALUATION_EVENTS,
            "by_cohort": {c: {
                "have_labelled_fixtures": len((per.get(c) or {}).get(
                    "fx") or ()),
                "labelled_rows": int((per.get(c) or {}).get("rows") or 0),
                "need": need,
                "shortfall": max(0, need - len((per.get(c) or {}).get(
                    "fx") or ()))}
                for c in (FM.COHORT_DISPLAYED, FM.COHORT_EXECUTABLE)}}


def _eval_summary(ev: dict) -> dict:
    doc = dict(ev.get("evaluation") or {})
    mc = dict(doc.get("market_comparison") or {})
    el = dict(doc.get("approval_eligibility") or {})
    pr = mc.get("predictors") or {}

    def _imp(k):
        v = mc.get(k) or {}
        return {"log_loss_improvement": v.get("log_loss_improvement"),
                "ci95": (v.get("log_loss_improvement_uncertainty")
                         or {}).get("ci95")}
    return {"ok": ev.get("ok"), "refusal": ev.get("refusal"),
            "prospective_fixtures": mc.get("n_events"),
            "log_loss": {k: (pr.get(k) or {}).get("log_loss") for k in pr},
            "brier": {k: (pr.get(k) or {}).get("brier") for k in pr},
            "model_vs_raw_venue_price": _imp("model_vs_raw_venue_price"),
            "model_vs_training_base_rate": _imp("model_vs_training_base_rate"),
            "model_vs_pinnacle": _imp("model_vs_pinnacle"),
            "eligible": el.get("eligible"), "failed": el.get("failed"),
            "input_distribution_shift": (el.get("input_distribution_shift")
                                         or {}).get("status"),
            "promoted": False}


ATTEMPT_REGISTERED = "REGISTERED"
ATTEMPT_DEGENERATE = "REGISTERED_DEGENERATE"
ATTEMPT_FIT_REFUSED = "FIT_REFUSED"
ATTEMPT_REG_REFUSED = "REGISTRATION_REFUSED"
ATTEMPT_RAISED = "RAISED"


def evaluation_cohort_for(cohort: str, *, declared_at: float) -> dict:
    """THE EVALUATION COHORT, DECLARED BEFORE A FIT AND FROZEN WITH IT.
    Stored in the model's training provenance, which migration 138's trigger
    makes immutable; a refit is a NEW model with its own declaration."""
    FM = _FM()
    return {"declared_before_fitting": True,
            "declared_at_epoch_s": float(declared_at),
            "model_key": FM.KEY_ENTRY_PAYOUT,
            "source": FM.SOURCE_RESEARCH_OBSERVATIONS,
            "cohorts": [str(cohort)],
            "evidence_class": FM.EVIDENCE_CLASS_LIVE,
            "prospective_window_start": (
                "THIS MODEL'S REGISTRATION INSTANT "
                "(bettor_funded_models.created_at, immutable)"),
            "rule": ("PROSPECTIVE_LIVE observations of these cohorts, decided, "
                     "recorded and resolved after registration, on fixtures "
                     "the fit could not see; RETROSPECTIVE_STORED rows never "
                     "count"),
            "never_changes": ("a refit is a new model with its own frozen "
                              "cohort; it never re-scores or replaces this "
                              "one")}


def _degenerate(fitted: dict, lab_rows, labels) -> str | None:
    """Why a fitted model says nothing, or None."""
    FM = _FM()
    if len({float(y) for y in labels}) < 2:
        return "SINGLE_CLASS_TRAINING_LABELS"
    try:
        m = FM.load(fitted["params"])
        preds = [float(m.predict(r)) for r in lab_rows]
    except Exception as exc:                                   # noqa: BLE001
        return "PREDICTIONS_RAISED:%s" % type(exc).__name__
    if preds and max(preds) - min(preds) < 1e-6:
        return "CONSTANT_PREDICTIONS_ON_THE_TRAINING_ROWS"
    return None


async def attempt_fit(conn, *, model_id: str, cohort: str, through,
                      run_id: str | None = None, now: float | None = None,
                      estimator: str | None = None) -> dict:
    """ONE ATTEMPT TO FIT THE ENTRY MODEL ON ONE COHORT, ALWAYS RECORDED.

    Declares the evaluation cohort BEFORE fitting (stored, frozen, in the
    provenance), fits through `bettor_funded_model.fit_from_records`,
    registers a CANDIDATE, and writes one `derek_research_model_attempts`
    row whatever happened -- refused, degenerate, raised or registered --
    with the training rows and fixtures, the attempted set's hash and, when
    registered, the records' hash. Never promotes. Never raises."""
    import datetime as _dt
    import hashlib
    FM = _FM()
    at = float(now if now is not None else time.time())
    th = _epoch(through)
    through_dt = _dt.datetime.fromtimestamp(th, _dt.timezone.utc)
    ecoh = evaluation_cohort_for(cohort, declared_at=at)
    out: dict[str, Any] = {"attempt_id": str(model_id), "cohort": cohort,
                           "run_id": run_id, "evaluation_cohort": ecoh,
                           "promoted": False}
    rows_n = fx_n = 0
    set_sha = hashlib.sha256(b"[]").hexdigest()
    outcome, refusal, records_sha, detail = ATTEMPT_RAISED, None, None, {}
    try:
        lab = await labelled_observations(conn, through=th,
                                          outcomes_through=th,
                                          cohorts=[cohort])
        if lab.get("ok"):
            rows_n, fx_n = int(lab["n"]), int(lab["n_events"])
            set_sha = hashlib.sha256(json.dumps(sorted(
                str(d) for d in lab["decision_ids"])).encode()).hexdigest()
        fit = await FM.fit_from_records(
            conn, through=through_dt, model_key=FM.KEY_ENTRY_PAYOUT,
            source=FM.SOURCE_RESEARCH_OBSERVATIONS, cohorts=[cohort],
            estimator=estimator or FM.SCHEDULED_ESTIMATOR,
            windows={"declared_before_fitting": True,
                     "evaluation_cohort": ecoh})
        out["fit"] = {k: fit.get(k) for k in ("ok", "refusal", "n_events",
                                               "train_rows")}
        if not fit.get("ok"):
            outcome, refusal = ATTEMPT_FIT_REFUSED, str(fit.get("refusal"))
        else:
            rows_n = int(fit.get("train_rows") or rows_n)
            fx_n = int(fit.get("n_events") or fx_n)
            reg = await FM.register(
                conn, model_id=str(model_id), model_version=str(model_id),
                fitted=fit, fit_through=through_dt,
                model_key=FM.KEY_ENTRY_PAYOUT)
            out["registration"] = {k: reg.get(k) for k in (
                "ok", "refusal", "why", "inserted")}
            if not reg.get("ok"):
                outcome = ATTEMPT_REG_REFUSED
                refusal = str(reg.get("refusal"))
            else:
                records_sha = fit["training_provenance"]["records_sha"]
                created = _epoch((reg.get("model") or {}).get("created_at"))
                detail["prospective_window_start_epoch_s"] = created
                why = _degenerate(fit, lab.get("rows") or [],
                                  lab.get("labels") or [])
                outcome = ATTEMPT_DEGENERATE if why else ATTEMPT_REGISTERED
                if why:
                    detail["degenerate_because"] = why
    except Exception as exc:                                   # noqa: BLE001
        outcome, refusal = ATTEMPT_RAISED, "%s: %s" % (
            type(exc).__name__, str(exc)[:200])
    out.update(outcome=outcome, refusal=refusal, train_rows=rows_n,
               train_fixtures=fx_n, attempted_set_sha=set_sha,
               records_sha=records_sha, detail=detail)
    try:
        out["recorded"] = await conn.fetchval(
            "INSERT INTO derek_research_model_attempts (attempt_id, run_id, "
            " model_key, source, cohort, attempted_at, fit_through, "
            " evaluation_cohort, outcome, train_rows, train_fixtures, "
            " attempted_set_sha, records_sha, refusal, detail) "
            "VALUES ($1,$2,$3,$4,$5,to_timestamp($6),to_timestamp($7),"
            " $8::jsonb,$9,$10,$11,$12,$13,$14,$15::jsonb) "
            "ON CONFLICT DO NOTHING RETURNING attempt_id",
            str(model_id), run_id, FM.KEY_ENTRY_PAYOUT,
            FM.SOURCE_RESEARCH_OBSERVATIONS, str(cohort), at, th,
            json.dumps(ecoh), outcome, rows_n, fx_n, set_sha, records_sha,
            refusal, json.dumps(detail, default=str)) is not None
    except Exception as exc:                                   # noqa: BLE001
        out["recorded"] = False
        out["record_error"] = type(exc).__name__
    return out


async def daily_model_run(conn, *, now: float) -> dict:
    """ONCE PER UTC DAY. When a cohort's labelled research fixtures reach
    MIN_TRAIN_EVENTS: ATTEMPT a fit on that cohort only (never pooled) --
    every attempt recorded, its evaluation cohort declared and frozen before
    fitting -- and evaluate the cohort's scheduled candidates, each on its
    OWN frozen cohort, against the raw venue price, Pinnacle and the base
    rate. Otherwise record INSUFFICIENT_LABELLED_FIXTURES with the exact
    counts. A refit happens only when the cohort has grown by
    CANDIDATE_REFIT_MIN_NEW_EVENTS fixtures since the last scheduled
    attempt, and is a NEW model. NEVER promotes. Never raises (the caller
    guards)."""
    FM = _FM()
    at = float(now)
    day = _day_of(at)
    run_id = "derek-research-run:%s" % day
    out: dict[str, Any] = {"run_day": str(day), "ran": False,
                           "promoted": False, "promotion": NEVER_PROMOTES}
    if not (await _regclass(conn, "derek_research_model_runs")
            and await _regclass(conn, "derek_research_observations")):
        return dict(out, refusal="RESEARCH_TABLES_ABSENT")
    prior = await conn.fetchrow(
        "SELECT run_id, outcome FROM derek_research_model_runs "
        " WHERE run_day = $1", day)
    if prior is not None:
        return dict(out, already_ran=True, run_id=prior["run_id"],
                    outcome=prior["outcome"])
    lab = await labelled_observations(conn, through=at, outcomes_through=at)
    fitted: dict = {}
    evaluations: dict = {}
    attempted: list = []
    if not lab.get("ok"):
        outcome, counts = RUN_LABELS_UNREADABLE, {"error": lab.get("error")}
    else:
        counts = labelled_counts(lab)
        ready = [c for c, b in counts["by_cohort"].items()
                 if b["have_labelled_fixtures"] >= FM.MIN_TRAIN_EVENTS]
        outcome = RUN_INSUFFICIENT
        for c in ready:
            prefix = "%s:%s:" % (AUTO_MODEL_PREFIX, c)
            prev = await conn.fetchrow(
                "SELECT attempt_id, train_fixtures "
                "  FROM derek_research_model_attempts "
                " WHERE attempt_id LIKE $1 ORDER BY attempted_at DESC "
                " LIMIT 1", prefix + "%")
            have = counts["by_cohort"][c]["have_labelled_fixtures"]
            last_n = None if prev is None else int(prev["train_fixtures"])
            grew = None if last_n is None else have - last_n
            if last_n is None or grew >= FM.CANDIDATE_REFIT_MIN_NEW_EVENTS:
                att = await attempt_fit(conn, model_id=prefix + str(day),
                                        cohort=c, through=at, run_id=run_id,
                                        now=at)
                attempted.append(att["attempt_id"])
                fitted[c] = {"refit": True,
                             "model_id": att["attempt_id"],
                             "outcome": att["outcome"],
                             "ok": att["outcome"] in (ATTEMPT_REGISTERED,
                                                      ATTEMPT_DEGENERATE),
                             "refusal": att["refusal"],
                             "training_rows": att["train_rows"],
                             "training_fixtures": att["train_fixtures"],
                             "records_sha": att["records_sha"],
                             "fit_through_epoch_s": at}
            else:
                fitted[c] = {"refit": False, "why": (
                    "%d new labelled fixture(s) since the last scheduled "
                    "attempt (%d); a refit needs %d"
                    % (grew, last_n, FM.CANDIDATE_REFIT_MIN_NEW_EVENTS))}
            cands = [r["model_id"] for r in await conn.fetch(
                "SELECT model_id FROM bettor_funded_models "
                " WHERE model_key = $1 AND model_id LIKE $2 AND state = $3 "
                " ORDER BY created_at DESC LIMIT $4",
                FM.KEY_ENTRY_PAYOUT, prefix + "%", FM.STATE_CANDIDATE,
                EVALUATE_LATEST)]
            for mid in cands:
                # ITS OWN FROZEN COHORT (read from its provenance by the
                # registry); never another model's.
                evaluations[mid] = _eval_summary(
                    await FM.evaluate(conn, model_id=mid, now=at))
        if ready:
            outcome = (RUN_FITTED if any(f.get("refit") and f.get("ok")
                                         for f in fitted.values())
                       else RUN_EVALUATED if evaluations else RUN_FIT_REFUSED)
    wrote = await conn.fetchval(
        "INSERT INTO derek_research_model_runs (run_id, run_day, ran_at, "
        " outcome, counts, fitted, evaluations, detail, attempted_model_ids) "
        "VALUES ($1, $2, to_timestamp($3), $4, $5::jsonb, $6::jsonb, "
        "        $7::jsonb, $8::jsonb, $9::text[]) "
        "ON CONFLICT DO NOTHING RETURNING run_id",
        run_id, day, at, outcome, json.dumps(counts, default=str),
        json.dumps(fitted, default=str), json.dumps(evaluations, default=str),
        json.dumps({"promotion": NEVER_PROMOTES,
                    "minimums": FM.qualification_minimums(
                        FM.KEY_ENTRY_PAYOUT),
                    "model_description": FM.ENTRY_PAYOUT_DESCRIPTION},
                   default=str), attempted)
    return dict(out, ran=wrote is not None, run_id=run_id, outcome=outcome,
                counts=counts, fitted=fitted, evaluations=evaluations,
                attempted_model_ids=attempted)


async def latest_model_run(conn) -> dict | None:
    """The most recent daily run, for the workspace. Raises on a failed
    read."""
    r = await conn.fetchrow(
        "SELECT run_id, run_day, ran_at, outcome, counts, fitted, "
        "       evaluations, promoted, attempted_model_ids "
        "  FROM derek_research_model_runs "
        " ORDER BY run_day DESC LIMIT 1")
    if r is None:
        return None
    return {"run_id": r["run_id"], "run_day": str(r["run_day"]),
            "ran_at": _epoch(r["ran_at"]), "outcome": r["outcome"],
            "counts": _j(r["counts"]), "fitted": _j(r["fitted"]),
            "evaluations": _j(r["evaluations"]),
            "attempted_model_ids": list(r["attempted_model_ids"] or []),
            "promoted": bool(r["promoted"]), "promotion": NEVER_PROMOTES}


# ═════════════════════════════════════════════════════════════════════════
# 6 · THE WORKSPACE SUMMARY
# ═════════════════════════════════════════════════════════════════════════

async def summary(conn) -> dict:
    """Counts by cohort, in fixtures and rows, with labels. Raises on a
    failed read (the workspace turns that into UNAVAILABLE)."""
    from .. import bettor_external_shadow as ext
    rows = await conn.fetch(
        "SELECT o.cohort, count(*) AS rows, count(DISTINCT o.fixture) AS fx,"
        "       count(DISTINCT o.fixture) FILTER (WHERE v.outcome_known "
        "             AND v.outcome IN (0, 1) "
        "             AND v.outcome_basis = ANY($2::text[])) AS labelled_fx,"
        "       count(*) FILTER (WHERE o.model_p IS NOT NULL) AS frozen,"
        "       max(o.decided_at) AS last_at "
        "  FROM derek_research_observations o "
        "  LEFT JOIN external_valuations v ON v.id = o.valuation_id "
        "   AND v.experiment_id = $1 "
        " GROUP BY 1 ORDER BY 1", ext.EXPERIMENT_ID, list(LABEL_BASES))
    return {"by_cohort": {r["cohort"]: {
        "rows": int(r["rows"]), "fixtures": int(r["fx"]),
        "labelled_fixtures": int(r["labelled_fx"]),
        "rows_with_a_frozen_model_prediction": int(r["frozen"]),
        "last_decided_at": _epoch(r["last_at"])} for r in rows},
        "never_pooled_silently": (
            "each cohort is counted, trained and evaluated under its own "
            "name; a model declares which cohort(s) it was trained on")}


def describe() -> dict:
    FM = _FM()
    return {"observer_version": OBSERVER_VERSION,
            "cohorts": {FM.COHORT_DISPLAYED: FM.PRICE_BASIS_DISPLAYED,
                        FM.COHORT_EXECUTABLE: FM.PRICE_BASIS_EXECUTABLE},
            "timing_uncertainty": list(TIMINGS),
            "label_bases": list(LABEL_BASES),
            "max_per_cycle": MAX_PER_CYCLE,
            "authorizes_orders": False,
            "execution_quality": "UNKNOWN"}
