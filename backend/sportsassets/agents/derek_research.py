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

A_NO_APPROVED_MODEL = "NO_APPROVED_MODEL_AT_THE_DECISION_INSTANT"
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


def observation_from_row(row: dict, *, approved: dict | None = None
                         ) -> tuple[dict | None, str | None]:
    """(observation, None) or (None, the named reason it is not one)."""
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
    feats = {"acquisition_price": round(float(price), 9),
             "payout_is_complement": (1.0 if r.get("payout_is_complement")
                                      else 0.0)}
    mdl = _frozen_model(approved, feats, decided_at=decided)
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
    }, None


# ═════════════════════════════════════════════════════════════════════════
# 2 · RECORDING (the scheduled path calls `observe_cycle`)
# ═════════════════════════════════════════════════════════════════════════

CANDIDATES_SQL = """
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
       AND v.decided_at > to_timestamp($2)
       AND v.decided_at <= to_timestamp($3)
       AND NOT EXISTS (SELECT 1 FROM derek_research_observations o
                        WHERE o.valuation_id = v.id)
     ORDER BY v.id
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
         model_absent_reason, observer_version)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,to_timestamp($13),
            $14,$15,$16,
            CASE WHEN $17::float8 IS NULL THEN NULL ELSE to_timestamp($17) END,
            CASE WHEN $18::float8 IS NULL THEN NULL ELSE to_timestamp($18) END,
            $19,$20,$21,$22::jsonb,$23,
            CASE WHEN $24::float8 IS NULL THEN NULL ELSE to_timestamp($24) END,
            CASE WHEN $25::float8 IS NULL THEN NULL ELSE to_timestamp($25) END,
            $26,$27,$28,$29::jsonb,$30,$31,$32,$33,$34,$35)
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
            OBSERVER_VERSION)


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
    for row in rows:
        try:
            obs, why = observation_from_row(row, approved=approved)
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
    out["ok"] = not out["errors"]
    return out


async def observe_cycle(conn, *, now: float, elapsed_s: float = 0.0) -> dict:
    """THE SCHEDULED STEP (`derek.after_cycle`): this cycle's window."""
    at = float(now)
    since = at - max(LOOKBACK_S, float(elapsed_s or 0.0) + 60.0)
    return await observe(conn, since=since, until=at + 1.0)


# ═════════════════════════════════════════════════════════════════════════
# 3 · THE LABELLER (called by bettor_funded_model.labelled)
# ═════════════════════════════════════════════════════════════════════════

LABEL_SQL = """
    SELECT o.observation_id, o.fixture, o.features, o.feature_sha,
           o.price, o.price_basis, o.cohort, o.pinnacle_p, o.record_purpose,
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
            "cohorts")
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
# 5 · THE WORKSPACE SUMMARY
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
